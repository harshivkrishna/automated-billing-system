import { useState, useEffect } from 'react'
import io from 'socket.io-client'

const InvoiceSystem = ({ theme, ipAddress }) => {
  const [detections, setDetections] = useState([])
  const [invoiceNumber] = useState(`INV-${Math.floor(Math.random() * 10000)}`)
  const [currentDate] = useState(new Date().toLocaleDateString())

  useEffect(() => {
    const socket = io(`http://${ipAddress}:5000`)

    socket.on('detection_update', (data) => {
      console.log('Received detection update:', data);
      if (data.products) {
        setDetections(data.products);
      }
    })

    return () => {
      socket.disconnect()
    }
  }, [ipAddress])

  const calculateTotal = () => {
    return detections.reduce((total, item) => total + (item.price * item.quantity), 0)
  }

  return (
    <div className={`${theme === 'dark' ? 'bg-[#1f2937]' : 'bg-white'} rounded-lg p-4 w-full h-full flex flex-col`}>
      <h2 className={`text-xl font-semibold mb-3 ${theme === 'dark' ? 'text-white' : 'text-gray-900'}`}>
        Invoice System
      </h2>
      
      <div className={`text-sm ${theme === 'dark' ? 'text-gray-300' : 'text-gray-600'} mb-4`}>
        <div>Invoice #: {invoiceNumber}</div>
        <div>Date: {currentDate}</div>
      </div>

      <div className="overflow-x-auto flex-grow">
        <table className="w-full">
          <thead>
            <tr className={`border-b ${theme === 'dark' ? 'border-gray-600' : 'border-gray-200'}`}>
              <th className={`text-left py-2 ${theme === 'dark' ? 'text-gray-300' : 'text-gray-600'}`}>Product</th>
              <th className={`text-right py-2 ${theme === 'dark' ? 'text-gray-300' : 'text-gray-600'}`}>Quantity</th>
              <th className={`text-right py-2 ${theme === 'dark' ? 'text-gray-300' : 'text-gray-600'}`}>Price</th>
            </tr>
          </thead>
          <tbody>
            {detections.map((item, index) => (
              <tr key={index} className={`border-b ${theme === 'dark' ? 'border-gray-600/30' : 'border-gray-200/30'}`}>
                <td className={`py-2 ${theme === 'dark' ? 'text-white' : 'text-gray-900'}`}>
                  {item.name}
                </td>
                <td className={`text-right py-2 ${theme === 'dark' ? 'text-white' : 'text-gray-900'}`}>
                  {item.quantity}
                </td>
                <td className={`text-right py-2 ${theme === 'dark' ? 'text-white' : 'text-gray-900'}`}>
                  ${(item.price * item.quantity).toFixed(2)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-auto space-y-2 pt-4">
        <div className={`flex justify-between font-semibold border-t ${theme === 'dark' ? 'border-gray-600 text-white' : 'border-gray-200 text-gray-900'} pt-2`}>
          <span>Total:</span>
          <span>${calculateTotal().toFixed(2)}</span>
        </div>
      </div>
    </div>
  )
}

export default InvoiceSystem
